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

Three independent things must hold for SMP cloth not to sink into the body, and
they fail separately, so a single "it clips" report cannot tell you which to fix:

  1. THE CLOTH CAN REACH A COLLIDER. FSMP lets two shapes collide only when
     BOTH sides allow it (hdtSkinnedMeshBody canCollideWith, called both ways).
     One side allows the other when the other carries a tag in its
     can-collide-with-tag list, or, when that list is EMPTY, when the other
     carries none of its no-collide-with-tag tags. Tags are engine strings,
     which compare without case.
  2. A BODY COLLIDER EXISTS in the NIF for it to collide against.
  3. THE REST POSE IS OUTSIDE THE BODY. This is the one nothing measured, and it
     is decisive: hdtSMP64's own maintainers call the sphere-triangle penetration
     path "obviously wrong", so cloth that STARTS inside the body is not reliably
     pushed out. Collision resolves approaching geometry, not existing overlap.

And one thing must NOT hold:

  4. NOT AN UNCONSTRAINED COLLISION PAIR (cloth that reaches a collider, with
     no constraint of any kind: generic, stiffspring, conetwist or a constraint
     group). That combination diverges and takes FSMP's collision SIMD out of
     bounds -- an equip CTD. It is why (1) cannot simply be auto-fixed
     everywhere: adding body collision to unconstrained cloth CAUSES the crash.

The population is what FSMP itself loads, and nothing else:
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
# Every constraint element FSMP's system reader accepts at the top level.
CONSTRAINT_KINDS = ("generic-constraint", "stiffspring-constraint",
                    "conetwist-constraint", "constraint-group")
PENETRATION_SAMPLE = 4000      # cap per shape; these meshes reach 30k+ verts

# Population exclusions. The first two are "no physics in game"; the rest are
# pieces that DECLARE physics but on which FSMP simulates no cloth.
NO_POINTER = "no physics pointer (no physics in game)"
MARKER_ONLY = "physics name in file, no root pointer"
UNRESOLVED = "pointer resolves to no loose file"
UNPARSEABLE = "xml unparseable"
NOT_SYSTEM = "xml root is not <system>"
NO_CLOTH = "xml has no per-vertex cloth"
CLOTH_ABSENT = "xml cloth names no shape in the NIF"
NIF_UNREADABLE = "nif unreadable"
DEAD = (UNRESOLVED, UNPARSEABLE, NOT_SYSTEM, CLOTH_ABSENT)
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


def parse_physics(root):
    return {
        "cloth": [_shape(el) for el in root.findall("per-vertex-shape")],
        "colliders": [_shape(el) for el in root.findall("per-triangle-shape")],
        "constrained": any(root.find(k) is not None for k in CONSTRAINT_KINDS),
    }


def classify(nif_path: Path, nif, notes=None):
    """(row, None) for a piece FSMP simulates cloth on, else (None, reason).
    `notes` (a Counter) counts the XMLs read only after dropping junk past the
    root, measured or not."""
    ptr = physics_pointer(nif)
    if not ptr:
        return None, NO_POINTER
    xml = resolve_pointer(ptr, nif_path)
    if xml is None:
        return None, UNRESOLVED
    root, repaired = parse_xml_bytes(xml.read_bytes())
    if repaired and notes is not None:
        notes[REPAIRED] += 1
    if root is None:
        return None, UNPARSEABLE
    if root.tag != "system":
        return None, NOT_SYSTEM
    info = parse_physics(root)
    if not info["cloth"]:
        return None, NO_CLOTH
    shape_names = {s.name for s in nif.shapes}
    cloth = [c for c in info["cloth"] if c["name"] in shape_names]
    if not cloth:
        return None, CLOTH_ABSENT
    present = [k for k in info["colliders"] if k["name"] in shape_names]
    return {
        "constrained": info["constrained"],
        "cloth": cloth,
        "colliders_named": len(info["colliders"]),
        "colliders": present,
    }, None


def cloth_reach(row):
    """Per cloth shape: (reaches any in-file collider, reaches a body-tagged
    one), under FSMP's mutual rule."""
    out = {}
    for c in row["cloth"]:
        hits = [k for k in row["colliders"] if collides(c, k)]
        out[c["name"]] = (bool(hits), any(k["tags"] & BODY_TAGS for k in hits))
    return out


def _penetration(nif, cloth_names):
    """Worst rest-pose depth of each cloth INSIDE the injected body, in units.

    Signed along the body's outward normal at the nearest body vertex; positive
    means the cloth vertex sits inside. Returns {} when the NIF carries no
    injected body -- reported as UNKNOWN rather than counted clean.
    """
    shapes = {s.name: s for s in nif.shapes}
    body = shapes.get("BaseShape")
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
        s = shapes.get(name)
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
    print(f"  DECLARED BUT NO CLOTH SIMULATES    : "
          f"{sum(skip[r] for r in dead):5d} / {len(dead_g)}   "
          f"(pointer unresolved, xml unreadable, or its cloth absent)")
    if notes and notes.get(REPAIRED):
        print(f"  xml read only after ignoring junk past </system>: "
              f"{notes[REPAIRED]} NIF(s)")

    reach = {r["path"]: cloth_reach(r) for r in rows}
    no_reach = [r for r in rows
                if any(not a for a, _b in reach[r["path"]].values())]
    no_body = [r for r in rows
               if any(not b for _a, b in reach[r["path"]].values())]
    none_named = [r for r in rows if not r["colliders_named"]]
    absent = [r for r in rows if len(r["colliders"]) < r["colliders_named"]]
    crash_class = [r for r in rows if not r["constrained"]
                   and any(a for a, _b in reach[r["path"]].values())]
    pen_unknown = [r for r in rows if r["pen"] is None]
    pen_known = [r for r in rows if r["pen"]]
    penetrating = [r for r in pen_known
                   if any(v[1] > 0 for v in r["pen"].values())]

    def line(label, rs, tail=""):
        print(f"  {label:<44}: {len(rs):5d} / {_g(rs):<4d}{tail}")

    print("\nFAULTS, NIFs / garments (a piece can carry more than one)")
    line("cloth reaching NO collider in its file", no_reach,
         f"   ({100*len(no_reach)/len(rows):.1f}%)")
    line("cloth reaching no BODY-tagged collider", no_body)
    line("   ...of those, CONSTRAINED (fixable safely)",
         [r for r in no_body if r["constrained"]])
    line("   ...of those, unconstrained (fix = equip CTD)",
         [r for r in no_body if not r["constrained"]])
    line("xml names no collider", none_named)
    line("collider named but ABSENT from the NIF", absent)
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
