"""The ZEROED BodySlide body, for CBBE (3BA) and for UBE -- the reference a
measurement of "where the author put the garment" has to use.

WHAT "ZEROED" MEANS HERE. What BodySlide builds with zeroed sliders: the slider
set's ShapeData base shape plus every slider at its slider-set DEFAULT for that
weight (`small` for `_0`, `big` for `_1`). A zeroed preset lists no sliders, so
the defaults are what it builds. It is NOT always the raw base mesh: 3BA's
defaults move the neck, wrist and ankle seams at weight 0, and UBE's put
`NipplesShowUp` on both weights and `SkinnyMorph` on weight 0. The game renders
those, so a reference without them would be wrong where they act.

WHY ZEROED. Garments built in BodySlide at zeroed sliders sit on this body, the
game loads it, and the preset arrives at runtime through body morphs on top of
it. A reference built at some other preset moves under every garment: measured
2026-09-21, the 3BA mod folder's own femalebody -- which name-based discovery
picked over the BodySlide output the game loads -- sits up to 1.97u off the
zeroed 3BA body over 16,061 torso vertices, and at weight 1 it grows THROUGH
garments built on the zeroed body.

RESOLUTION, NEVER BY MOD NAME:
  1. find the body's SLIDER SETS by what they build (OutputPath + OutputFile) --
     that output is also the mesh path the race's skin references;
  2. keep only the body FAMILY asked for, by base topology (3BA 18,436 verts,
     UBE 29,298): a BHUNP or CBBE SE body builds the same femalebody path and
     must not come back as the 3BA reference;
  3. build the zeroed geometry from the ShapeData base NIF and OSD, the way
     BodySlide does (defaults /100, invert, uv and zap sliders, local and
     shared slider data);
  4. find the file the GAME loads at that output path: MO2's overwrite, then
     enabled mods from highest priority down, then the game's Data folder;
  5. accept that file only if it IS the zeroed build, to TOL on every vertex.
Otherwise `ZeroedBodyError` says which step failed and by how much. It
subclasses FileNotFoundError on purpose: callers already treat that as "no
reference body for this weight -- skip, never substitute another". Every
failure inside the search -- an unreadable NIF or OSD in some unrelated slider
set included -- is recorded and turned into that error, never raised raw.

Weight 0 must come from the same folder as weight 1 (the sibling rule of
`canonical_body.weight_sibling`): a half-installed pair is refused.
"""
from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import nif_io
from . import paths as _paths
from .osd import OsdFile

TOL = 1e-4          # float32 round-off; a real build matched at 0.00000u

# What each body's slider set builds, and the base topology that identifies the
# family. Paths are compared lowercased with forward slashes; "_0"/"_1" is
# appended to the file for each weight.
KINDS = {
    "cbbe": ("meshes/actors/character/character assets", "femalebody", 18436, "CBBE 3BA"),
    "ube": ("meshes/!ube/body", "femalebody_tangent", 29298, "UBE"),
}
_WEIGHTS = ("_0", "_1")
_SLIDERSETS = "calientetools/bodyslide/slidersets"
_SHAPEDATA = "calientetools/bodyslide/shapedata"

# (kind, weight, dirs) -> ZeroedBody, and ("builds", kind, weight, dirs) ->
# the slider-set builds `_builds` made for that instance.
_CACHE: "dict[tuple, object]" = {}


class ZeroedBodyError(FileNotFoundError):
    """No zeroed body could be established for this kind and weight."""


@dataclass(frozen=True)
class ZeroedBody:
    path: Path            # the file the game loads -- verified to BE the zeroed build
    shape: str            # the body shape's name in that file
    slider_set: str       # the BodySlide slider set it was checked against
    max_dev: float        # worst vertex deviation from the zeroed build (<= TOL)


def _norm(rel: str) -> str:
    return rel.replace("\\", "/").strip("/").lower()


def _shape_verts(nif_path: Path, shape: str) -> "np.ndarray | None":
    """Vertex positions of one named shape, or None if the NIF lacks it."""
    nf = nif_io.open_nif_retry(str(nif_path))
    for s in nf.shapes:
        if s.name == shape:
            return np.asarray(s.verts, dtype=np.float64)
    return None


def _shape_sizes(nif_path: Path) -> "dict[str, int]":
    nf = nif_io.open_nif_retry(str(nif_path))
    return {s.name: len(s.verts) for s in nf.shapes}


class _Vfs:
    """Loose-file resolution over `dirs`, highest priority first -- MO2's
    overwrite, the enabled mods, then the game Data folder. Case-insensitive,
    like the game's file system."""

    def __init__(self, dirs: "list[Path]"):
        self.dirs = list(dirs)

    def winner(self, rel: str) -> "tuple[Path, Path] | None":
        """(file, the dir that provides it) for a Data-relative path, or None."""
        parts = [p for p in rel.replace("\\", "/").split("/") if p]
        for d in self.dirs:
            p = _ci_join(d, parts)
            if p is not None:
                return p, d
        return None

    def walk(self, rel: str) -> "dict[str, Path]":
        """Every file under a Data-relative directory, subfolders included
        (BodySlide loads slider sets recursively), keyed by its lowercased
        path below that directory; the highest provider wins per key."""
        parts = [p for p in rel.split("/") if p]
        out: "dict[str, Path]" = {}
        for d in self.dirs:
            sub = _ci_join(d, parts, want_dir=True)
            if sub is None:
                continue
            try:
                for e in sub.rglob("*"):
                    if e.is_file():
                        out.setdefault(e.relative_to(sub).as_posix().lower(), e)
            except OSError:
                continue
        return out


def _ci_join(base: Path, parts: "list[str]", want_dir: bool = False) -> "Path | None":
    """Join `parts` onto `base` matching each component case-insensitively."""
    cur = base
    for i, part in enumerate(parts):
        cand = cur / part
        last = i == len(parts) - 1
        if (cand.is_dir() if (want_dir or not last) else cand.is_file()):
            cur = cand
            continue
        if os.name == "nt":
            return None     # the direct check above is already case-insensitive
        try:
            low = part.lower()
            hit = next((e for e in cur.iterdir() if e.name.lower() == low), None)
        except OSError:
            return None
        if hit is None:
            return None
        if (want_dir or not last) and not hit.is_dir():
            return None
        if last and not want_dir and not hit.is_file():
            return None
        cur = hit
    return cur


def _slider_sets(vfs: _Vfs, out_path: str, out_file: str) -> "list[ET.Element]":
    """Every slider set, in the WINNING copy of each slider-set file, that
    builds `out_path/out_file`."""
    found = []
    for key, osp in sorted(vfs.walk(_SLIDERSETS).items()):
        if not key.endswith((".osp", ".xml")):
            continue
        try:
            text = osp.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if out_file not in text.lower():
            continue                      # cheap reject before parsing
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for ss in root.iter("SliderSet"):
            op = _norm(ss.findtext("OutputPath") or "")
            of = (ss.findtext("OutputFile") or "").strip().lower()
            if op == out_path and of == out_file:
                found.append(ss)
    return found


def _zeroed_build(vfs: _Vfs, ss: ET.Element, weight: str) -> "tuple[str, np.ndarray]":
    """(body shape name, zeroed vertices) for one slider set at one weight.

    The body shape is named as BodySlide names it in the built file: the base
    NIF's shape NAME (the <Shape> text). Its `target` attribute is only the key
    slider data is filed under."""
    set_name = ss.get("name", "?")
    folder = (ss.findtext("DataFolder") or "").strip()
    src = (ss.findtext("SourceFile") or "").strip()
    of = ss.find("OutputFile")
    if of is not None and (of.get("GenWeights", "true").lower() != "true"):
        raise ZeroedBodyError(f"slider set {set_name!r} does not build weights")
    base = vfs.winner(f"{_SHAPEDATA}/{folder}/{src}")
    if base is None:
        raise ZeroedBodyError(
            f"slider set {set_name!r}: ShapeData base {folder}/{src} not found")
    base = base[0]
    # <Shape target="T" DataFolder="A;B">NAME</Shape>
    shapes = {}
    for e in ss.findall("Shape"):
        name = (e.text or "").strip()
        shapes[name] = (e.get("target", name),
                        [f.strip() for f in (e.get("DataFolder") or "").split(";")
                         if f.strip()])
    sizes = _shape_sizes(base)
    present = [n for n in shapes if n in sizes]
    if not present:
        raise ZeroedBodyError(f"slider set {set_name!r}: none of its shapes "
                              f"are in {base.name}")
    name = max(present, key=lambda n: sizes[n])      # the body is the largest
    target, shape_folders = shapes[name]
    # A COPY: the defaults are added in place below, and a reader that hands
    # back a cached array would otherwise carry one weight's build into the next.
    verts = np.array(_shape_verts(base, name), dtype=np.float64)
    key = "small" if weight == "_0" else "big"
    osds: "dict[Path, dict]" = {}
    for sl in ss.findall("Slider"):
        raw = float(sl.get(key, "0") or 0)
        if sl.get("zap", "false").lower() == "true":
            if raw != 0.0:          # a zap deletes vertices: nothing to compare
                raise ZeroedBodyError(f"slider set {set_name!r}: zap slider "
                                      f"{sl.get('name')!r} is on by default, so "
                                      f"its build cannot be checked vertex by vertex")
            continue
        val = 100.0 - raw if sl.get("invert", "false").lower() == "true" else raw
        if val == 0.0 or sl.get("uv", "false").lower() == "true":
            continue                      # no geometry at this weight
        for d in sl.findall("Data"):
            if d.get("target") != target:
                continue
            ref = (d.text or "").strip().replace("\\", "/")
            if ".osd/" not in ref.lower():
                raise ZeroedBodyError(f"slider set {set_name!r}: default slider "
                                      f"{sl.get('name')!r} uses unsupported data {ref!r}")
            fname, morph = ref.rsplit("/", 1)
            # BodySlide: local data lives in the set's own folder; shared data
            # in the shape's DataFolder(s), else the set's folder.
            local = d.get("local", "false").lower() == "true"
            folders = [folder] if local else (shape_folders or [folder])
            hit = next((h for h in (vfs.winner(f"{_SHAPEDATA}/{f}/{fname}")
                                    for f in folders) if h is not None), None)
            if hit is None:
                raise ZeroedBodyError(f"slider set {set_name!r}: {fname} not "
                                      f"found in {folders}")
            if hit[0] not in osds:
                osds[hit[0]] = OsdFile.load(hit[0]).by_name()
            m = osds[hit[0]].get(morph)
            if m is None:
                raise ZeroedBodyError(f"slider set {set_name!r}: morph {morph!r} "
                                      f"missing from {fname}")
            if len(m.idx) and int(m.idx.max()) >= len(verts):
                raise ZeroedBodyError(f"slider set {set_name!r}: morph {morph!r} "
                                      f"indexes past the base shape")
            np.add.at(verts, m.idx.astype(np.int64),
                      (val / 100.0) * m.delta.astype(np.float64))
    return name, verts


def _layout_dirs() -> "list[Path]":
    """The discovered instance as VFS dirs: overwrite, enabled mods highest
    priority first, then the game Data folder(s)."""
    lay = _paths.discover_layout()
    order = _paths.enabled_mods_ordered(lay)
    if lay.mods_root is None or order is None:
        raise ZeroedBodyError("no MO2 profile found (set CBBE2UBE_MO2_INI): "
                              "without load order it cannot be said which body "
                              "the game loads")
    ow = _paths.overwrite_dir(lay)
    return _dirs(Path(lay.mods_root), order, ow, lay.game_data_dirs)


def _dirs(mods_root, order, overwrite=None, data_dirs=()) -> "list[Path]":
    ow = [Path(overwrite)] if overwrite is not None and Path(overwrite).is_dir() else []
    return ow + [Path(mods_root) / m for m in order] + [Path(d) for d in data_dirs]


def _builds(vfs: _Vfs, kind: str, weight: str, memo: dict):
    """(builds, unusable, any_set) for `kind` at `weight` on this instance.

    builds = [(slider set, body shape name, zeroed verts)] for every slider set
    of the right FAMILY that could be built; unusable = one note per set that
    could not (or is another body family); any_set = whether any slider set
    builds the output path at all. Built ONCE per kind, weight and instance and
    reused for every file checked against it -- the expensive part of the
    resolver is here, not in the comparison."""
    key = ("builds", kind, weight, tuple(str(d) for d in vfs.dirs))
    if key in memo:
        return memo[key]
    out_path, out_file, body_verts, label = KINDS[kind]
    sets = _slider_sets(vfs, out_path, out_file)
    builds, unusable = [], []
    for ss in sets:
        set_name = ss.get("name", "?")
        try:
            name, zv = _zeroed_build(vfs, ss, weight)
        except ZeroedBodyError as e:
            unusable.append(str(e))
            continue
        except Exception as e:                # a broken, unrelated set: record it
            unusable.append(f"slider set {set_name!r}: unreadable "
                            f"({type(e).__name__}: {e})")
            continue
        if len(zv) != body_verts:
            unusable.append(f"slider set {set_name!r}: a {len(zv)}-vertex body, "
                            f"not {label} ({body_verts})")
            continue
        builds.append((set_name, name, zv))
    memo[key] = (builds, unusable, bool(sets))
    return memo[key]


def _compare(file: Path, builds) -> "tuple[tuple | None, list[str]]":
    """(match, notes): the first build `file` IS, within TOL, as
    (slider set, shape name, max deviation) -- else None, with one note per
    build saying why not."""
    notes = []
    for set_name, name, zv in builds:
        gv = _shape_verts(file, name)
        if gv is None or gv.shape != zv.shape:
            notes.append(f"{set_name!r}: {Path(file).name} has no {name!r} "
                         f"shape of {len(zv)} verts")
            continue
        dev = np.linalg.norm(gv - zv, axis=1)
        if float(dev.max()) <= TOL:
            return (set_name, name, float(dev.max())), notes
        notes.append(f"{set_name!r}: off by up to {dev.max():.3f}u on "
                     f"{int((dev > TOL).sum())} of {len(dev)} verts")
    return None, notes


def zeroed_body(kind: str, weight: str = "_1", *, mods_root=None, order=None,
                overwrite=None, data_dirs=()) -> ZeroedBody:
    """The game-loaded `kind` body at `weight`, verified to be BodySlide's
    zeroed build. Raises ZeroedBodyError otherwise -- never returns a
    different body instead. Pass `mods_root` and `order` together (tests,
    an explicit instance), or neither (the discovered one)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}, not {kind!r}")
    if weight not in _WEIGHTS:
        raise ValueError(f"weight must be '_0' or '_1', not {weight!r}")
    if (mods_root is None) != (order is None):
        raise ValueError("pass mods_root and order together, or neither -- a "
                         "half-given instance would be silently replaced")
    dirs = (_layout_dirs() if mods_root is None
            else _dirs(mods_root, order, overwrite, data_dirs))
    ck = (kind, weight, tuple(str(d) for d in dirs))
    if ck in _CACHE:
        return _CACHE[ck]
    vfs = _Vfs(dirs)
    out_path, out_file, body_verts, label = KINDS[kind]
    rel = f"{out_path}/{out_file}{weight}.nif"
    game = vfs.winner(rel)
    if game is None:
        raise ZeroedBodyError(f"nothing in the load order provides {rel} -- "
                              f"build the {label} body in BodySlide")
    game_file, provider = game
    if weight == "_0":
        w1 = vfs.winner(f"{out_path}/{out_file}_1.nif")
        if w1 is None or w1[1] != provider:
            raise ZeroedBodyError(
                f"{rel} comes from {provider.name!r} but its weight-1 sibling "
                f"does not -- a half-installed pair is not a reference")
    try:
        _shape_sizes(game_file)
    except Exception as e:
        raise ZeroedBodyError(f"{game_file} is unreadable "
                              f"({type(e).__name__}: {e})") from e
    builds, unusable, any_set = _builds(vfs, kind, weight, _CACHE)
    if not any_set:
        raise ZeroedBodyError(f"no BodySlide slider set builds {out_path}/"
                              f"{out_file} -- install the body's BodySlide files")
    match, compared = _compare(game_file, builds)
    if match is not None:
        set_name, name, dev = match
        # realpath restores the on-disk casing the lowercase lookup lost
        zb = ZeroedBody(Path(os.path.realpath(game_file)), name, set_name, dev)
        _CACHE[ck] = zb
        # Once per body and weight per process: every run states what it
        # measured against. stderr, so no parsed report gains a line.
        print(f"[zeroed-body] {kind}{weight}: {zb.path} -- slider set "
              f"{zb.slider_set!r}, max deviation {zb.max_dev:.1e}u",
              file=sys.stderr, flush=True)
        return zb
    where = f"{game_file} (from {provider.name!r})"
    if compared:
        raise ZeroedBodyError(
            f"{where} is NOT a zeroed BodySlide build of any {label} slider set "
            f"-- rebuild it with zeroed sliders. Checked: "
            + "; ".join(compared + unusable))
    raise ZeroedBodyError(
        f"{where} could not be checked against any {label} slider set that "
        f"builds it: " + "; ".join(unusable))


# --- Listing every candidate body, for the pick dialog ----------------------

@dataclass(frozen=True)
class BodyCandidate:
    """One body a user could pick for `kind`: a weight PAIR from one folder."""
    kind: str
    provider: str          # the mod folder, "MO2 overwrite", "game Data", or an override label
    path_1: "Path | None"
    path_0: "Path | None"
    game_loads: bool       # the game loads THIS pair (both weights come from here)
    status: str            # zeroed | not-zeroed | unchecked | other-family | half-pair | unreadable | missing
    selectable: bool       # a same-family, complete, readable pair
    slider_set: str        # the set it matched ("" when it matched none)
    max_dev: "float | None"  # worst deviation from the zeroed build over both weights
    reason: str            # why it is not a verified zeroed build ("" when it is)


@dataclass(frozen=True)
class BodyListing:
    kind: str
    label: str
    candidates: "tuple[BodyCandidate, ...]"   # load order, highest first; overrides last
    default: "BodyCandidate | None"            # the verified zeroed pair the game loads
    notes: "tuple[str, ...]"                   # slider sets that could not be used, and why


_OFF_BY = re.compile(r"off by up to ([0-9.]+)u")


def _provider_label(d: Path) -> str:
    low = d.name.lower()
    if low == "overwrite":
        return "MO2 overwrite"
    if low == "data":
        return "game Data folder"
    return d.name


def _candidate(vfs, kind, provider, pair, game_loads, memo) -> BodyCandidate:
    """Check one weight pair against the kind's zeroed builds. The status rules
    mirror what zeroed_body() will and will not accept."""
    body_verts, label = KINDS[kind][2], KINDS[kind][3]
    p1, p0 = pair.get("_1"), pair.get("_0")

    def cand(status, selectable, reason, slider_set="", max_dev=None):
        real = lambda p: Path(os.path.realpath(p)) if p is not None else None
        return BodyCandidate(kind, provider, real(p1), real(p0), game_loads,
                             status, selectable, slider_set, max_dev, reason)

    if p1 is None and p0 is None:
        return cand("missing", False, "names a file that does not exist")
    if p1 is None or p0 is None:
        have = "weight 1" if p1 is not None else "weight 0"
        return cand("half-pair", False, f"only the {have} file is here -- a "
                    f"half-installed pair is not a reference")
    for p in (p1, p0):
        try:
            sizes = _shape_sizes(p)
        except Exception as e:
            return cand("unreadable", False, f"{Path(p).name} is unreadable "
                        f"({type(e).__name__})")
        # Family = SOME shape of the body's size, not the largest one: an extra
        # bigger shape is no reason to refuse a body zeroed_body() accepts.
        if body_verts not in sizes.values():
            n = max(sizes.values(), default=0)
            return cand("other-family", False, f"a {n}-vertex body, not {label} "
                        f"({body_verts})")
    # BOTH weights are judged before any verdict: a weight that cannot be
    # checked must not hide one already measured as not zeroed.
    matched, devs, why, unchecked = [], [], [], []
    for w, p in (("_1", p1), ("_0", p0)):
        builds, unusable, _any = _builds(vfs, kind, w, memo)
        if not builds:
            unchecked.append(f"weight {w[-1]}: " + (
                unusable[0] if unusable else "no slider set builds it"))
            continue
        match, notes = _compare(p, builds)
        if match is None:
            why.append(f"weight {w[-1]}: " + "; ".join(notes))
            # the CLOSEST set's worst vertex; None when no set had a comparable
            # shape, so nothing was measured
            offs = [float(x) for x in _OFF_BY.findall(" ".join(notes))]
            devs.append(min(offs) if offs else None)
        else:
            matched.append(match[0])
            devs.append(match[2])
    if why:
        # "up to" only when EVERY weight produced a number
        worst = None if unchecked or None in devs else max(devs)
        return cand("not-zeroed", True, "not the zeroed build -- " + "; ".join(
            why + [u + " (could not be checked)" for u in unchecked]), max_dev=worst)
    if unchecked:
        return cand("unchecked", True, "cannot be checked: " + "; ".join(unchecked))
    return cand("zeroed", True, "", matched[0], max(devs))


def list_bodies(kinds=("cbbe", "ube"), *, mods_root=None, order=None,
                overwrite=None, data_dirs=(), extra=()) -> "dict[str, BodyListing]":
    """Every body the user could pick, per kind, each checked against the
    zeroed builds -- for the dialog that lets the user confirm or change the
    reference bodies before a conversion.

    Candidates: every ENABLED provider of the kind's output path (MO2
    overwrite, enabled mods by priority, the game Data folder), plus `extra`
    = [(kind, label, path_1)] for bodies an override already names. Not
    offered: BodySlide ShapeData templates (one file for both weights, never
    loaded by the game) and NPC-specific femalebody files (not the race body).
    The default is exactly what zeroed_body() returns at both weights.

    A FRESH build memo per call: the dialog runs in a long-lived GUI process,
    and BodySlide can rebuild the files between two conversions."""
    if (mods_root is None) != (order is None):
        raise ValueError("pass mods_root and order together, or neither")
    dirs = (_layout_dirs() if mods_root is None
            else _dirs(mods_root, order, overwrite, data_dirs))
    vfs = _Vfs(dirs)
    memo: dict = {}
    listings = {}
    for kind in kinds:
        out_path, out_file, _verts, label = KINDS[kind]
        parts = {w: [p for p in f"{out_path}/{out_file}{w}.nif".split("/") if p]
                 for w in _WEIGHTS}
        winner = {w: vfs.winner("/".join(parts[w])) for w in _WEIGHTS}
        cands, seen = [], set()
        for d in dirs:
            pair = {w: _ci_join(d, parts[w]) for w in _WEIGHTS}
            if pair["_0"] is None and pair["_1"] is None:
                continue
            loads = all(winner[w] is not None and winner[w][1] == d for w in _WEIGHTS)
            c = _candidate(vfs, kind, _provider_label(d), pair, loads, memo)
            cands.append(c)
            seen.add(str(c.path_1).lower())
        for k, lab, p in extra:
            if k != kind or not p:
                continue
            p1 = Path(p)
            if str(Path(os.path.realpath(p1))).lower() in seen:
                continue                      # already listed as a provider
            # A name with no weight is used AS-IS at both weights by the
            # converter (_ube_body_override) -- list it as the pair it acts as.
            one_file = not p1.stem.endswith(("_0", "_1"))
            p0 = p1 if one_file else p1.with_name(p1.stem[:-2] + "_0" + p1.suffix)
            pair = {"_1": p1 if p1.is_file() else None,
                    "_0": p0 if p0.is_file() else None}
            c = _candidate(vfs, kind, lab, pair, False, memo)
            if one_file and c.status not in ("missing", "unreadable"):
                note = "one file, used at both weights"
                c = replace(c, reason=f"{c.reason}; {note}" if c.reason else note)
            cands.append(c)
            seen.add(str(Path(os.path.realpath(p1))).lower())
        default = next((c for c in cands if c.game_loads and c.status == "zeroed"), None)
        notes = []
        for w in _WEIGHTS:
            for n in _builds(vfs, kind, w, memo)[1]:
                if n not in notes:
                    notes.append(n)
        listings[kind] = BodyListing(kind, label, tuple(cands), default, tuple(notes))
    return listings
