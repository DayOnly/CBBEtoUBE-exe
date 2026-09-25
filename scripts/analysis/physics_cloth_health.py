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

Three independent things must hold for SMP cloth not to sink into the body, and
they fail separately, so a single "it clips" report cannot tell you which to fix:

  1. THE CLOTH CAN REACH A PARTNER. FSMP never pairs two kinematic shapes
     (needsCollision), and otherwise lets two shapes collide only when BOTH
     sides allow it (canCollideWith, called both ways). One side allows the
     other when the other carries a tag in its can-collide-with-tag list, or,
     when that list is EMPTY, when the other carries none of its
     no-collide-with-tag tags. Tags are engine strings, which compare without
     case. Every kind pairs with every kind: vertex-vertex, vertex-triangle and
     triangle-triangle.
  2. A BODY COLLIDER EXISTS in the NIF for it to collide against: a KINEMATIC
     shape carrying a body tag (a simulated shape tagged "body" is not the body).
  3. THE REST POSE IS OUTSIDE THE BODY. This is the one nothing measured, and it
     is decisive: hdtSMP64's own maintainers call the sphere-triangle penetration
     path "obviously wrong", so cloth that STARTS inside the body is not reliably
     pushed out. Collision resolves approaching geometry, not existing overlap.

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
<shared> (it limits pairs ACROSS files, and every pair here is inside one);
per-bone collision filters (can/no-collide-with-bone, weight-threshold);
disable-tag; and whether a declared bone's node exists in the skeleton. The
body-collider test is a tag-name list (BODY_TAGS), and the rest-pose depth uses
raw bind vertices against the injected body.

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
every per-triangle-shape for a collider: they are not comparable either.
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
# The constraint elements FSMP's system reader accepts, at the top level and
# inside a <constraint-group>.
CONSTRAINT_KINDS = ("generic-constraint", "stiffspring-constraint",
                    "conetwist-constraint")
CONSTRAINT_GROUP = "constraint-group"
SHAPE_KINDS = ("per-vertex-shape", "per-triangle-shape")
PENETRATION_SAMPLE = 4000      # cap per shape; these meshes reach 30k+ verts

# Population exclusions. The first two are "no physics in game"; the rest are
# pieces that DECLARE physics but on which the model finds no simulated shape.
NO_POINTER = "no physics pointer (no physics in game)"
MARKER_ONLY = "physics name in file, no root pointer"
UNRESOLVED = "pointer resolves to no loose file"
XML_UNREADABLE = "xml unreadable (read failed)"
UNPARSEABLE = "xml unparseable"
NOT_SYSTEM = "xml root is not <system>"
NO_SHAPES = "xml declares no collision shape"
SHAPES_ABSENT = "xml shapes name no skinned NIF shape"
NO_DYNAMIC = "every shape is kinematic (mass-0 bones)"
NIF_UNREADABLE = "nif unreadable"
# What the game itself would load no shape from. XML_UNREADABLE is NOT here:
# a failed read is this run's gap, not a fact about the piece.
DEAD = (UNRESOLVED, UNPARSEABLE, NOT_SYSTEM, SHAPES_ABSENT)
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
    return {(t.text or "").strip().lower() for t in el.findall(kind)}


def _shape(el):
    return {"name": el.get("name") or "?",
            "tags": _tags(el, "tag"),
            "can": _tags(el, "can-collide-with-tag"),
            "no": _tags(el, "no-collide-with-tag")}


def allows(a, b) -> bool:
    """FSMP canCollideWith, one direction: may shape `a` collide with `b`?
    An EMPTY can-collide list means every tag but a's no-collide tags."""
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
    "constrained": whether any constraint joins at least one dynamic bone}."""
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
    return {"shapes": shapes, "named": named, "constrained": constrained}


def classify(nif_path: Path, nif, notes=None):
    """(row, None) for a piece the model finds a simulated shape on, else
    (None, reason). `notes` (a Counter) counts the XMLs read only after
    dropping junk past the root, measured or not."""
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
    info = read_system(root, _skin(nif))
    if not info["named"]:
        return None, NO_SHAPES
    if not info["shapes"]:
        return None, SHAPES_ABSENT
    cloth = [s for s in info["shapes"] if s["dynamic"]]
    if not cloth:
        return None, NO_DYNAMIC
    return {
        "constrained": info["constrained"],
        "cloth": cloth,
        "shapes": info["shapes"],
        "shapes_named": info["named"],
    }, None


def cloth_reach(row):
    """Per cloth shape: (reaches any other in-file shape, reaches a KINEMATIC
    body-tagged one), under FSMP's pairing rule."""
    out = {}
    for c in row["cloth"]:
        hits = [k for k in row["shapes"] if k is not c and pair_collides(c, k)]
        out[c["name"]] = (bool(hits), any(
            not k["dynamic"] and k["tags"] & BODY_TAGS for k in hits))
    return out


def _penetration(nif, cloth_names):
    """Worst rest-pose depth of each cloth INSIDE the injected body, in units.

    Signed along the body's outward normal at the nearest body vertex; positive
    means the cloth vertex sits inside. Returns {} when the NIF carries no
    injected body -- reported as UNKNOWN rather than counted clean.
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
        v = np.array(s.verts, np.float64)
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
        row["pen"] = _penetration(nif, [c["name"] for c in row["cloth"]])
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

    kept = [r for r in skip if r not in (NO_POINTER, MARKER_ONLY,
                                          NIF_UNREADABLE)]
    declared = len(rows) + sum(skip[r] for r in kept)
    declared_g = {r["garment"] for r in rows}.union(
        *(skip_garments[r] for r in kept))
    dead = [r for r in DEAD if r in skip]
    dead_g = set().union(*(skip_garments[r] for r in dead))
    print(f"\n  pieces with a root physics pointer : {declared:5d} / "
          f"{len(declared_g)}")
    print(f"  DECLARED BUT NO SHAPE LOADS        : "
          f"{sum(skip[r] for r in dead):5d} / {len(dead_g)}   "
          f"(pointer unresolved, xml unparseable, or its shapes absent)")
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
    none_named = [r for r in rows if r["shapes_named"] < 2]
    absent = [r for r in rows if len(r["shapes"]) < r["shapes_named"]]
    crash_class = [r for r in rows if not r["constrained"]
                   and any(a for a, _b in reach[r["path"]].values())]
    pen_unknown = [r for r in rows if r["pen"] is None]
    pen_known = [r for r in rows if r["pen"]]
    penetrating = [r for r in pen_known
                   if any(v[1] > 0 for v in r["pen"].values())]

    def line(label, rs, tail=""):
        print(f"  {label:<44}: {len(rs):5d} / {_g(rs):<4d}{tail}")

    print("\nFAULTS, NIFs / garments (a piece can carry more than one)")
    line("cloth reaching NO partner in its file", no_reach,
         f"   ({100*len(no_reach)/len(rows):.1f}%)")
    line("cloth reaching no kinematic BODY-tagged shape", no_body)
    line("   ...of those, CONSTRAINED (fixable safely)",
         [r for r in no_body if r["constrained"]])
    line("   ...of those, unconstrained (fix = equip CTD)",
         [r for r in no_body if not r["constrained"]])
    line("xml names no second shape", none_named)
    line("shape named but ABSENT from the NIF", absent)
    line("unconstrained collision pair", crash_class,
         "   <- known equip-CTD pattern")
    print(f"\n  REST POSE INSIDE THE BODY        : {len(penetrating):5d}"
          f"   of {len(pen_known)} measurable"
          + (f"   ({len(pen_unknown)} have no injected body -> UNKNOWN, "
             f"not counted clean)" if pen_unknown else ""))

    if penetrating:
        worst = []
        for r in penetrating:
            for nm, (mx, cnt, tot) in r["pen"].items():
                if cnt:
                    worst.append((mx, cnt, tot, nm, r["path"]))
        worst.sort(reverse=True)
        print("\n  WORST REST-POSE PENETRATION (this is what collision cannot fix)")
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
