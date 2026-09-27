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

"""Physics-cloth health across the pack: WHY a simulated garment clips the body.

WHAT IS CLOTH. FSMP does not decide by element kind. A <per-vertex-shape> and a
<per-triangle-shape> differ only in collision geometry (a sphere per vertex, or
a triangle); either one SIMULATES when any of its skin bones is dynamic, and is
a kinematic COLLIDER when every skin bone is kinematic (mass 0). A bone's mass
is modelled by replaying the XML in document order, as FSMP's reader does:
  * <bone-default name=N extends=E> copies template E (unknown -> the unnamed
    default) and overrides it with its own <mass>; with no name it REPLACES the
    unnamed default for everything after it.
  * <bone name=B template=T> takes template T (unknown -> the unnamed default)
    plus its own <mass>. The first declaration of a bone wins.
  * a skin bone, or a constraint body, not declared before the shape or
    constraint that first uses it, is created from the unnamed default AS IT
    STANDS AT THAT POINT, so it can be dynamic.
Here "cloth" means a simulated shape of either kind.

CLOTH MOVED BY BONES ALONE. FSMP keeps a system when it has any BONE
(SkinnedMeshSystem::valid() is `!m_bones.empty()`), not only when it has a
shape, and drives every bone it created with mass > 0 (writeTransform skips
only kinematic ones). So an XML of bones and constraints with NO simulated
shape -- none declared, the declared ones absent from the NIF, or every one
kinematic -- still swings the visible NIF mesh skinned to those bones, and that
mesh has no collision geometry at all: cloth reaching no partner, the
strongest no-reach case. Such a piece is MEASURED: its "moved" shapes are the
NIF shapes skinned DIRECTLY to a bone the replay creates with mass > 0 (the
injected body excluded; a shape skinned only to an undeclared child node of
such a bone is not seen here -- physics_rest_depth.py follows the node tree).
No constraint is required: FSMP moves an unconstrained dynamic bone too (it
falls), and the row's "constrained" flag splits the two as for any cloth. A
piece whose dynamic bones carry no NIF shape is counted apart (nothing visible
moves), and so is one where no bone simulates.

Three independent things must hold for SMP cloth not to sink into the body, and
they fail separately, so a single "it clips" report cannot tell you which to fix:

  1. THE CLOTH CAN REACH A PARTNER. FSMP never pairs two kinematic shapes
     (needsCollision), and otherwise lets two shapes collide only when BOTH
     sides allow it (canCollideWith, called both ways). One side allows the
     other when the other carries a tag in its can-collide-with-tag list, or,
     when that list is EMPTY, when the other carries none of its
     no-collide-with-tag tags. Tags are engine strings, which compare without
     case. Every kind pairs with every kind: vertex-vertex, vertex-triangle and
     triangle-triangle. A shape marked <shared>external</shared> pairs with
     nothing in its own file: FSMP's SHARED_EXTERNAL refuses a partner on the
     same skeleton, and every shape of one file shares one (public, internal
     and private all allow in-file pairs).
  2. A BODY COLLIDER EXISTS in the NIF for it to collide against: a KINEMATIC
     shape that is the body or stands in for it (a simulated shape is never
     the body). It is either tagged with a body tag (BODY_TAGS), or a BODY
     STAND-IN: its name or a tag carries a body-part token (BODY_TOKENS:
     "vbody", "VirtualLegs", "ButtCol", ...) AND more than half its skin
     WEIGHT is on the actor's body bones (an `NPC ` bone other than the root
     and COM). Both, because a ground plane tagged "legs" rides the root only, and
     a greaves or belt collider on body bones carries no body-part name.
  3. THE REST POSE IS OUTSIDE THE BODY. It is decisive: hdtSMP64's own
     maintainers call the sphere-triangle penetration path "obviously wrong", so
     cloth that STARTS inside the body is not reliably pushed out. Collision
     resolves approaching geometry, not existing overlap. The row here reads
     STORED vertices, which is not the rest pose: `#chain-rest-lift` moves chain
     root nodes, not vertices. The rest pose, through the node tree and the
     skin, is `physics_rest_depth.py`. Only the vertices FSMP moves are read
     (weight > 0 on a dynamic bone): a shape is cloth when ANY skin bone is
     dynamic, and its vertices on kinematic bones alone are rigid mesh.

And one thing must NOT hold:

  4. NOT AN UNCONSTRAINED COLLISION PAIR (cloth that reaches a partner, with
     no constraint of any kind: generic, stiffspring, conetwist, at the top
     level or inside a constraint group). A constraint between two kinematic
     bones is skipped by FSMP and does not count. That combination diverges and
     takes FSMP's collision SIMD out of bounds -- an equip CTD. It is why (1)
     cannot simply be auto-fixed everywhere: adding body collision to
     unconstrained cloth CAUSES the crash.

These counts are a MODEL of FSMP, not a run of it. Not modelled: colliders
another worn piece brings; shape-name physics from defaultBBPs.xml and its
shape-name remapping; XMLs that exist only in an archive; bone renames;
per-bone collision filters (can/no-collide-with-bone, weight-threshold);
disable-tag; and whether a declared bone's node exists in the skeleton.
<shared> is modelled only as far as it acts inside one file (external); it
and the tags are read untrimmed, as FSMP's readText returns them (its
GetValue is outside the copied source, and no live XML pads either). The
body-collider test is by name (tags, and name plus body-bone skin for a
stand-in), not by where the shape lies, and the depth row uses stored
vertices against the injected body (see 3).

The population is modelled on what FSMP itself loads, and nothing else:
  * the NIF's OWN pointer -- the first `HDT Skinned Mesh Physics Object` string
    on the ROOT node, as FSMP's scanBBP reads it -- resolved with the
    converter's resolver (a leading `Data\\` is handled the way the converter
    handles it). A NIF with no pointer has no physics in game, whatever XML
    sits beside it: there is NO filename match here.
  * the XML's BYTES, so a UTF-8 byte-order mark parses. Junk after the root
    close is ignored, as FSMP stops reading at `</system>`. A default
    `xmlns` changes no element name for FSMP, so none is kept here. A root
    other than <system> loads nothing.

Usage:
    python scripts/analysis/physics_cloth_health.py <output meshes dir> [limit]

Reports the population accounting first. A shrinking denominator is how a census
flatters itself, so every exclusion is counted and printed. Numbers produced
before 2026-09-25 used a filename match, a text read, namespaced tags, a
one-sided tag rule and one constraint kind: they are not comparable with these.
Numbers from the first 09-25 version took every per-vertex-shape for cloth and
every per-triangle-shape for a collider: they are not comparable either. Numbers
from the second left out cloth moved by bones alone and knew a body collider
only by its tag: its measured set and its no-partner and no-body rows are not
comparable with these. Stored-depth numbers from before the dynamic-weight
rule read every vertex of a cloth shape, rigid ones included.
"""
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# Canonical spelling so test_analysis_repo_root can verify the level.
_REPO = Path(__file__).resolve().parent.parent.parent
REPO = _REPO
sys.path.insert(0, str(_REPO / ".pynifly"))
sys.path.insert(0, str(_REPO))

import numpy as np                                     # noqa: E402
from scipy.spatial import cKDTree                      # noqa: E402
from src import hdt_xml_gen                            # noqa: E402
from src import nif_convert as nc                      # noqa: E402

PHYSICS_EXTRA = "HDT Skinned Mesh Physics Object"
_MARKER = PHYSICS_EXTRA.lower().encode("ascii")
BODY_TAGS = {"body", "body2", "colbody", "bodycol"}
# A body STAND-IN's name or tag carries one of these (substring, no case), AND
# more than BODY_SKIN_SHARE of its skin WEIGHT is on the actor's body bones. From
# the kinematic partners live cloth reaches (09-24 pack): vbody, vbd,
# VirtualBody, VirtualLegs, VirtualFeet, VirtualButt, VirtualArms,
# VirtualHands, CollisionLegs, ButtCol, LegsCol, PantsC, ColPants. NOT a body
# part: ground, collision, greaves, skirt, belt, cape, necklace, head, box,
# armor, boot ("arm" alone would match "armor").
BODY_TOKENS = ("body", "vbd", "leg", "feet", "foot", "butt", "thigh", "calf",
               "arms", "hand", "pant", "torso", "breast", "belly")
BODY_SKIN_SHARE = 0.5
# Skeleton bones that are not the body's surface: a shape riding only these
# (a ground plane on the root) does not lie on the body.
NOT_BODY_BONES = ("npc root [root]", "npc com [com ]")
# The converter's injected body is the body, never garment cloth.
BODY_SHAPE_KEYS = tuple(n.lower() for n in nc.UBE_BODY_INJECT_NAMES)
# The constraint elements FSMP's system reader accepts, at the top level and
# inside a <constraint-group>.
CONSTRAINT_KINDS = ("generic-constraint", "stiffspring-constraint",
                    "conetwist-constraint")
CONSTRAINT_GROUP = "constraint-group"
SHAPE_KINDS = ("per-vertex-shape", "per-triangle-shape")
PENETRATION_SAMPLE = 4000      # cap per shape; these meshes reach 30k+ verts

# Population exclusions. The first two are "no physics in game"; the rest are
# pieces that DECLARE physics but on which nothing visible moves.
NO_POINTER = "no physics pointer (no physics in game)"
MARKER_ONLY = "physics name in file, no root pointer"
UNRESOLVED = "pointer resolves to no loose file"
XML_UNREADABLE = "xml unreadable (read failed)"
UNPARSEABLE = "xml unparseable"
NOT_SYSTEM = "xml root is not <system>"
NO_SHAPES = "xml declares no shape, no bone simulates"
SHAPES_ABSENT = "xml shapes not in the NIF, no bone simulates"
NO_DYNAMIC = "every bone kinematic (mass 0): nothing moves"
BONES_UNSKINNED = "bones simulate, no NIF shape skinned to one"
NIF_UNREADABLE = "nif unreadable"
# FSMP builds no system from these. XML_UNREADABLE is NOT here: a failed read
# is this run's gap, not a fact about the piece.
DEAD = (UNRESOLVED, UNPARSEABLE, NOT_SYSTEM)
# The system may load, but no visible shape moves: no bone simulates, or the
# ones that do carry no NIF shape.
STILL = (NO_SHAPES, SHAPES_ABSENT, NO_DYNAMIC, BONES_UNSKINNED)
# Why a piece moved by bones alone has no simulated collision shape.
WHY_NONE = "xml declares no shape"
WHY_ABSENT = "xml shapes not in the NIF"
WHY_KINEMATIC = "every xml shape kinematic"
REPAIRED = "junk after the root ignored"


def garment_of(rel: str) -> str:
    """Both weight halves of one garment are one garment."""
    rel = rel.replace("\\", "/").lower()
    stem = rel[:-4] if rel.endswith(".nif") else rel
    if stem.endswith(("_0", "_1")):
        stem = stem[:-2]
    return stem


def physics_pointer(nif):
    """The NIF's OWN physics pointer, read the way FSMP's scanBBP reads it: the
    first string extra-data on the ROOT node with that name and a value. The
    name is an engine string, so case does not matter to FSMP either.
    None when there is none -- then FSMP simulates nothing on this NIF."""
    try:
        for ed in nif.rootNode.extra_data():
            if ((getattr(ed, "name", None) or "").lower() == _MARKER.decode()
                    and getattr(ed, "string_data", None)):
                return ed.string_data
    except Exception:
        return None
    return None


def resolve_pointer(ptr: str, nif_path: Path):
    """The converter's own resolver for an authored pointer, with its
    #physics-data-prefix handling. Never a filename match: an XML that merely
    shares the NIF's stem is not what FSMP loads."""
    try:
        hit = nc._resolve_data_rel_in_vfs(ptr, nif_path)
    except Exception:
        return None
    return Path(hit) if hit is not None and Path(hit).is_file() else None


def parse_xml_bytes(data: bytes):
    """(root element or None, repaired). Parses the BYTES, so a UTF-8
    byte-order mark and the file's own encoding declaration are honoured; a
    text read through the locale turns the mark into junk before the root.
    Junk after the root close is dropped (`sanitise_hdt_xml_bytes`), because
    FSMP stops reading at the root's end tag.

    Namespaces are stripped from every tag: FSMP's reader matches the name as
    written, so a validator schema's `xmlns="..."` on <system> changes nothing
    for it, while ElementTree would rename every element `{uri}system`."""
    root = hdt_xml_gen._hdt_xml_parse_check(data)
    repaired = False
    if root is None:
        fixed, note = hdt_xml_gen.sanitise_hdt_xml_bytes(data)
        if note is None:
            return None, False
        root, repaired = hdt_xml_gen._hdt_xml_parse_check(fixed), True
        if root is None:
            return None, False
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.rsplit("}", 1)[-1]
    return root, repaired


def _tags(el, kind):
    # FSMP's readText returns the element's value as written, untrimmed;
    # tags are engine strings, which compare without case.
    return {(t.text or "").lower() for t in el.findall(kind)}


def _shape(el):
    shared = el.findall("shared")
    return {"name": el.get("name") or "?",
            "tags": _tags(el, "tag"),
            "can": _tags(el, "can-collide-with-tag"),
            "no": _tags(el, "no-collide-with-tag"),
            # FSMP reads it with readText, which does not trim, and compares
            # the string exactly (" external " is unknown to it, so public);
            # the last element read wins.
            "shared": (shared[-1].text or "") if shared else "public"}


def allows(a, b) -> bool:
    """FSMP canCollideWith, one direction: may shape `a` collide with `b`?
    An EMPTY can-collide list means every tag but a's no-collide tags.
    Both shapes are in one file, so on one skeleton: an `external` shape
    refuses every one of them (SHARED_EXTERNAL); the other values allow it."""
    if a.get("shared") == "external":
        return False
    if not a["can"]:
        return not (b["tags"] & a["no"])
    return bool(b["tags"] & a["can"])


def collides(a, b) -> bool:
    """FSMP runs canCollideWith BOTH ways; either side can refuse."""
    return allows(a, b) and allows(b, a)


def pair_collides(a, b) -> bool:
    """FSMP needsCollision: two kinematic shapes are never paired, whatever
    their tags; any other pair (of any shape kinds) needs the mutual rule."""
    if not a["dynamic"] and not b["dynamic"]:
        return False
    return collides(a, b)


def _mass(el, base: float) -> float:
    m = el.find("mass")
    if m is None:
        return base
    try:
        return float((m.text or "").strip())
    except ValueError:
        return base


def _key(name) -> str:
    """Node, bone, shape and template names are engine strings: the game's
    string pool folds case, so `[PElv]` in an XML is the skeleton's `[Pelv]`."""
    return (name or "").lower()


def _is_body_bone(name) -> bool:
    k = _key(name)
    return k.startswith("npc ") and k not in NOT_BODY_BONES


def body_skin_share(shape) -> float:
    """The share of a NIF shape's skin WEIGHT on the actor's body bones (an
    `NPC ` skeleton bone other than the root and COM). By weight, not by bone
    count: a leg collider can list six skirt bones that carry 6% of it. With
    no weights to read, the share of its skin bones."""
    try:
        weights = getattr(shape, "bone_weights", None) or {}
    except Exception:
        weights = {}
    total = body = 0.0
    for bone, pairs in weights.items():
        w = sum(float(x) for _v, x in pairs)
        total += w
        if _is_body_bone(bone):
            body += w
    if total > 0:
        return body / total
    bones = getattr(shape, "bone_names", None) or ()
    if not bones:
        return 0.0
    return sum(1 for b in bones if _is_body_bone(b)) / len(bones)


def is_body_collider(k) -> bool:
    """A KINEMATIC shape that is the body or stands in for it: a body tag, or
    a body-part token in its name or a tag AND mostly body-bone skin."""
    if k["dynamic"]:
        return False
    if k["tags"] & BODY_TAGS:
        return True
    words = [k["name"].lower(), *k["tags"]]
    named = any(t in w for w in words for t in BODY_TOKENS)
    return named and k.get("body_skin", 0.0) > BODY_SKIN_SHARE


def _skin(nif):
    """{shape name: skin bone names}, both by `_key`, for the NIF shapes FSMP
    can build a body from: skinned, with vertices (generateMeshBody skips the
    rest)."""
    out = {}
    for s in nif.shapes:
        bones = [_key(b) for b in (getattr(s, "bone_names", None) or ())]
        if bones and len(s.verts):
            out.setdefault(_key(s.name), bones)
    return out


def read_system(root, skin):
    """Replay the XML in document order, as FSMP's system reader does, to
    learn which shapes simulate. `skin` is `_skin(nif)`.

    Returns {"shapes": the XML shapes the NIF carries, each with "kind" and
    "dynamic"; "named": how many shape elements the XML declares;
    "constrained": whether any constraint joins at least one dynamic bone;
    "dynamic_bones": the `_key` of every bone FSMP creates with mass > 0}."""
    templates = {"": 0.0}             # bone-default name -> mass
    bones = {}                        # bone name -> mass; first one wins

    def template(name):
        return templates.get(_key(name), templates[""])

    def bone(name):
        # An undeclared bone is created from the unnamed default as it stands.
        if name not in bones:
            bones[name] = templates[""]
        return bones[name]

    def constraint(el):
        a, b = _key(el.get("bodyA")), _key(el.get("bodyB"))
        if not a or not b or a == b:
            return False
        ma, mb = bone(a), bone(b)
        return ma > 0 or mb > 0       # FSMP skips a kinematic-kinematic one

    shapes, named, constrained = [], 0, False
    for el in root:
        if el.tag == "bone":
            name = _key(el.get("name"))
            if name and name not in bones:
                bones[name] = _mass(el, template(el.get("template")))
        elif el.tag == "bone-default":
            templates[_key(el.get("name"))] = _mass(
                el, template(el.get("extends")))
        elif el.tag in SHAPE_KINDS:
            named += 1
            skinned = skin.get(_key(el.get("name")))
            if not skinned:
                continue              # FSMP builds no body for it
            masses = [bone(b) for b in skinned]
            shapes.append(dict(_shape(el), kind=el.tag,
                               dynamic=any(m > 0 for m in masses)))
        elif el.tag in CONSTRAINT_KINDS:
            constrained = constraint(el) or constrained
        elif el.tag == CONSTRAINT_GROUP:
            for sub in el:
                if sub.tag in CONSTRAINT_KINDS:
                    constrained = constraint(sub) or constrained
    return {"shapes": shapes, "named": named, "constrained": constrained,
            "dynamic_bones": {b for b, m in bones.items() if m > 0}}


def moved_shapes(skin, dynamic_bones):
    """The NIF shapes (by `_key`) skinned directly to a bone FSMP creates with
    mass > 0: what a bone-driven system visibly swings. The injected body is
    the body, not cloth."""
    return sorted(s for s, bones in skin.items()
                  if s not in BODY_SHAPE_KEYS and set(bones) & dynamic_bones)


def classify(nif_path: Path, nif, notes=None):
    """(row, None) for a piece on which the model finds something that
    simulates, else (None, reason). A row is either COLLISION cloth ("cloth"
    lists its simulated shapes) or cloth MOVED BY BONES ALONE ("cloth" is
    empty, "moved" names the NIF shapes on dynamic bones, "no_collision" says
    why there is no simulated shape). `notes` (a Counter) counts the XMLs read
    only after dropping junk past the root, measured or not."""
    ptr = physics_pointer(nif)
    if not ptr:
        return None, NO_POINTER
    xml = resolve_pointer(ptr, nif_path)
    if xml is None:
        return None, UNRESOLVED
    try:
        data = xml.read_bytes()
    except OSError:
        return None, XML_UNREADABLE
    root, repaired = parse_xml_bytes(data)
    if repaired and notes is not None:
        notes[REPAIRED] += 1
    if root is None:
        return None, UNPARSEABLE
    if root.tag != "system":
        return None, NOT_SYSTEM
    skin = _skin(nif)
    info = read_system(root, skin)
    by_key = {}
    for s in nif.shapes:
        by_key.setdefault(_key(s.name), s)
    for s in info["shapes"]:
        if not s["dynamic"]:          # only a kinematic shape can be the body
            s["body_skin"] = body_skin_share(by_key.get(_key(s["name"])))
    row = {
        "constrained": info["constrained"],
        "dynamic_bones": info["dynamic_bones"],
        "cloth": [s for s in info["shapes"] if s["dynamic"]],
        "moved": [],
        "no_collision": None,
        "shapes": info["shapes"],
        "shapes_named": info["named"],
    }
    if row["cloth"]:
        return row, None
    if not info["named"]:
        why, still = WHY_NONE, NO_SHAPES
    elif not info["shapes"]:
        why, still = WHY_ABSENT, SHAPES_ABSENT
    else:
        why, still = WHY_KINEMATIC, NO_DYNAMIC
    # FSMP keeps a system that has bones and drives every dynamic one, so
    # the NIF mesh on them swings with no collision shape of its own.
    moved = moved_shapes(skin, info["dynamic_bones"])
    if moved:
        row.update(moved=moved, no_collision=why)
        return row, None
    if info["dynamic_bones"]:
        return None, BONES_UNSKINNED
    return None, still


def cloth_reach(row):
    """Per cloth shape: (reaches any other in-file shape, reaches a KINEMATIC
    body collider -- `is_body_collider`), under FSMP's pairing rule. A shape
    moved by bones alone has no collision geometry: it reaches nothing."""
    out = {}
    for c in row["cloth"]:
        hits = [k for k in row["shapes"] if k is not c and pair_collides(c, k)]
        out[c["name"]] = (bool(hits), any(is_body_collider(k) for k in hits))
    for name in row.get("moved", ()):
        out[name] = (False, False)
    return out


def _kinematic_reach(row):
    """Per collision-cloth shape: (reaches ANY kinematic shape, reaches one
    carrying a body TAG, reaches one riding the body bones) -- how much of
    the body-collider row the stand-in rule moved, and what the rest does
    reach."""
    out = {}
    for c in row["cloth"]:
        hits = [k for k in row["shapes"] if k is not c and not k["dynamic"]
                and pair_collides(c, k)]
        out[c["name"]] = (
            bool(hits), any(k["tags"] & BODY_TAGS for k in hits),
            any(k.get("body_skin", 0.0) > BODY_SKIN_SHARE for k in hits))
    return out


def simulated_verts(shape, dynamic_bones):
    """Boolean mask of the shape's vertices FSMP moves: those with weight > 0
    on a DYNAMIC bone (`dynamic_bones`, by `_key`). A shape counts as cloth
    when ANY of its skin bones is dynamic, but a vertex weighted only to
    kinematic bones is rigid mesh the solver never moves. No weights to read:
    no vertex is known to move."""
    n = len(shape.verts)
    mask = np.zeros(n, bool)
    try:
        weights = getattr(shape, "bone_weights", None) or {}
    except Exception:
        weights = {}
    for bone, pairs in weights.items():
        if _key(bone) not in dynamic_bones:
            continue
        for vi, w in pairs:
            vi = int(vi)
            if 0 <= vi < n and float(w) > 0:
                mask[vi] = True
    return mask


def _penetration(nif, cloth_names, dynamic_bones):
    """Worst STORED-vertex depth of each cloth INSIDE the injected body, in
    units, over its SIMULATED vertices only (`simulated_verts`). Not the rest
    pose -- a lifted chain root moves the drawn cloth and not these vertices;
    `physics_rest_depth.py` measures that.

    Signed along the body's outward normal at the nearest body vertex; positive
    means the cloth vertex sits inside. Returns None when the NIF carries no
    injected body -- reported as UNKNOWN rather than counted clean -- and
    leaves out a shape with no simulated vertex ({} when no shape has one).
    """
    shapes = {}
    for s in nif.shapes:
        shapes.setdefault(_key(s.name), s)   # XML names match without case
    body = shapes.get(_key("BaseShape"))
    if body is None:
        return None
    bv = np.array(body.verts, np.float64)
    bt = np.array(body.tris, np.int64)
    if len(bv) == 0 or len(bt) == 0:
        return None
    bn = nc._vertex_normals_from_tris(bv, bt)
    tree = cKDTree(bv)
    out = {}
    for name in cloth_names:
        s = shapes.get(_key(name))
        if s is None:
            continue
        v = np.array(s.verts, np.float64).reshape(-1, 3)
        v = v[simulated_verts(s, dynamic_bones)]
        if len(v) == 0:
            continue
        if len(v) > PENETRATION_SAMPLE:
            step = max(1, len(v) // PENETRATION_SAMPLE)
            v = v[::step]
        _d, i = tree.query(v, k=1)
        signed = np.einsum("ij,ij->i", v - bv[i], bn[i])
        inside = -signed                      # positive = inside the body
        out[name] = (float(inside.max()), int((inside > 0.05).sum()), len(v))
    return out


def _open_nif(p: Path):
    return nc._pynifly().NifFile(str(p))


def scan(nifs, root: Path):
    """(rows, skip Counter, garments per exclusion reason, notes Counter)."""
    skip = Counter()
    skip_garments = {}
    notes = Counter()
    rows = []
    for k, p in enumerate(nifs):
        if k % 400 == 0:
            print(f"  ...{k}/{len(nifs)}", flush=True)
        rel = str(p.relative_to(root)).replace("\\", "/")
        reason = None
        try:
            has_marker = _MARKER in p.read_bytes().lower()
        except OSError:
            has_marker, reason = False, NIF_UNREADABLE
        if reason is None and not has_marker:
            reason = NO_POINTER       # no string of that name anywhere in it
        nif = None
        if reason is None:
            try:
                nif = _open_nif(p)
            except Exception:
                reason = NIF_UNREADABLE
        row = None
        if reason is None:
            row, reason = classify(p, nif, notes)
            if reason == NO_POINTER:
                reason = MARKER_ONLY
        if row is None:
            skip[reason] += 1
            skip_garments.setdefault(reason, set()).add(garment_of(rel))
            continue
        row["path"] = rel
        row["garment"] = garment_of(rel)
        row["pen"] = _penetration(
            nif, [c["name"] for c in row["cloth"]] or row["moved"],
            row["dynamic_bones"])
        rows.append(row)
    return rows, skip, skip_garments, notes


def _g(rows):
    return len({r["garment"] for r in rows})


def report(rows, skip, skip_garments, walked: int, notes=None) -> int:
    print("\n" + "=" * 72)
    print("POPULATION ACCOUNTING (NIFs / garments)")
    print("=" * 72)
    print(f"  NIFs walked                : {walked}")
    for reason, n in skip.most_common():
        print(f"  EXCLUDED {reason:<38}: {n:5d} / "
              f"{len(skip_garments.get(reason, ()))}")
    print(f"  MEASURED simulated pieces  : {len(rows)} / {_g(rows)}")
    if not rows:
        # Exit 3, not 0: an empty measured set is not a clean one.
        from scripts.analysis._census_common import require_population
        require_population(rows, "simulated piece(s)")
        raise SystemExit(3)             # require_population already exits 3
    by_bones = [r for r in rows if not r["cloth"]]
    print(f"     ...with a simulated collision shape   : "
          f"{len(rows) - len(by_bones):5d} / {_g([r for r in rows if r['cloth']])}")
    print(f"     ...moved by bones alone, no such shape: "
          f"{len(by_bones):5d} / {_g(by_bones)}")

    kept = [r for r in skip if r not in (NO_POINTER, MARKER_ONLY,
                                          NIF_UNREADABLE)]
    declared = len(rows) + sum(skip[r] for r in kept)
    declared_g = {r["garment"] for r in rows}.union(
        *(skip_garments[r] for r in kept))
    print(f"\n  pieces with a root physics pointer : {declared:5d} / "
          f"{len(declared_g)}")
    for label, group, why in (
            ("DECLARED BUT NO SYSTEM LOADS", DEAD,
             "pointer unresolved, xml unparseable or not <system>"),
            ("DECLARED BUT NOTHING VISIBLE MOVES", STILL,
             "no bone simulates, or no NIF shape on one")):
        hit = [r for r in group if r in skip]
        print(f"  {label:<35}: {sum(skip[r] for r in hit):5d} / "
              f"{len(set().union(*(skip_garments[r] for r in hit)))}   "
              f"({why})")
    if skip.get(XML_UNREADABLE):
        print(f"  !! {skip[XML_UNREADABLE]} NIF(s) whose xml could not be READ "
              f"this run: every count below is short by them")
    if notes and notes.get(REPAIRED):
        print(f"  xml read only after ignoring junk past </system>: "
              f"{notes[REPAIRED]} NIF(s)")
    n_dyn = sum(len(r["cloth"]) for r in rows)
    n_all = sum(len(r["shapes"]) for r in rows)
    print(f"  shapes on measured pieces: {n_dyn} simulated, {n_all - n_dyn} "
          f"kinematic (colliders); dynamics MODELLED from bone mass")

    reach = {r["path"]: cloth_reach(r) for r in rows}
    no_reach = [r for r in rows
                if any(not a for a, _b in reach[r["path"]].values())]
    no_body = [r for r in rows
               if any(not b for _a, b in reach[r["path"]].values())]
    kin = {r["path"]: _kinematic_reach(r) for r in rows if r["cloth"]}
    # Collision cloth with no body collider: does it reach ANY kinematic shape?
    nb_cloth = [r for r in no_body if r["cloth"]]
    nb_other = [r for r in nb_cloth if all(
        kin[r["path"]][c][0] for c, (_a, b) in reach[r["path"]].items()
        if not b)]
    # ...and of those, cloth that does reach a collider riding the body bones
    # but named for no body part (a generic "collision", a greaves proxy).
    nb_unnamed = [r for r in nb_other if any(
        kin[r["path"]][c][2] for c, (_a, b) in reach[r["path"]].items()
        if not b)]
    # Cloth whose body collider is a stand-in only: the tag list alone would
    # have counted it as reaching no body.
    by_standin = [r for r in rows if r["cloth"] and any(
        b and not kin[r["path"]][c][1]
        for c, (_a, b) in reach[r["path"]].items())]
    none_named = [r for r in rows if r["cloth"] and r["shapes_named"] < 2]
    absent = [r for r in rows if len(r["shapes"]) < r["shapes_named"]]
    crash_class = [r for r in rows if not r["constrained"]
                   and any(a for a, _b in reach[r["path"]].values())]
    pen_unknown = [r for r in rows if r["pen"] is None]
    pen_known = [r for r in rows if r["pen"]]
    # Neither: the body is there, but no cloth vertex has dynamic weight.
    pen_none = [r for r in rows if r["pen"] is not None and not r["pen"]]
    penetrating = [r for r in pen_known
                   if any(v[1] > 0 for v in r["pen"].values())]

    def line(label, rs, tail=""):
        print(f"  {label:<44}: {len(rs):5d} / {_g(rs):<4d}{tail}")

    print("\nFAULTS, NIFs / garments (a piece can carry more than one)")
    line("cloth reaching NO partner in its file", no_reach,
         f"   ({100*len(no_reach)/len(rows):.1f}%)")
    line("   ...simulated by bones, no dynamic collision shape", by_bones)
    for why in (WHY_NONE, WHY_ABSENT, WHY_KINEMATIC):
        rs = [r for r in by_bones if r["no_collision"] == why]
        if rs:
            line(f"        ({why})", rs)
    line("   ...a simulated shape that reaches nothing",
         [r for r in no_reach if r["cloth"]])
    line("cloth reaching no kinematic BODY collider", no_body)
    line("   ...of those, CONSTRAINED (fixable safely)",
         [r for r in no_body if r["constrained"]])
    line("   ...of those, unconstrained (fix = equip CTD)",
         [r for r in no_body if not r["constrained"]])
    line("   ...of those, moved by bones (no collision shape)",
         [r for r in no_body if not r["cloth"]])
    line("   ...of those, reaching only non-body kinematic shapes", nb_other)
    line("        (one on body bones, no body-part name)", nb_unnamed)
    line("        (only shapes off the body bones: ground)",
         [r for r in nb_other if r not in nb_unnamed])
    line("   ...of those, a shape reaching no kinematic shape",
         [r for r in nb_cloth if r not in nb_other])
    line("cloth whose body collider is a STAND-IN only", by_standin,
         "   (no body tag; body-part name on body bones)")
    line("xml names no second shape (collision cloth)", none_named)
    line("shape named but ABSENT from the NIF", absent)
    line("unconstrained collision pair", crash_class,
         "   <- known equip-CTD pattern")
    print(f"\n  STORED CLOTH VERTS INSIDE THE BODY: {len(penetrating):5d}"
          f"   of {len(pen_known)} measurable"
          + (f"   ({len(pen_unknown)} have no injected body -> UNKNOWN, "
             f"not counted clean)" if pen_unknown else "")
          + (f"   ({len(pen_none)} have no vertex weighted to a dynamic "
             f"bone)" if pen_none else ""))
    print("  (stored vertices FSMP moves -- weight > 0 on a dynamic bone -- NOT "
          "the rest pose: a lifted chain root moves the drawn cloth; "
          "physics_rest_depth.py measures that)")

    if penetrating:
        worst = []
        for r in penetrating:
            for nm, (mx, cnt, tot) in r["pen"].items():
                if cnt:
                    worst.append((mx, cnt, tot, nm, r["path"]))
        worst.sort(reverse=True)
        print("\n  WORST STORED-VERTEX DEPTH (bind position; not the rest pose)")
        for mx, cnt, tot, nm, path in worst[:20]:
            print(f"     {mx:6.2f}u  {cnt:5d}/{tot:<5d} verts inside  "
                  f"{nm[:18]:<18} {path}")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    nifs = sorted(root.rglob("*.nif"))
    if limit:
        nifs = nifs[:limit]
    rows, skip, skip_garments, notes = scan(nifs, root)
    return report(rows, skip, skip_garments, len(nifs), notes)


if __name__ == "__main__":
    raise SystemExit(main())
